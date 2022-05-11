node {
    stage 'Retrieve sources'
    checkout([
        $class: 'GitSCM',  branches: [[name: 'refs/heads/'+env.BRANCH_NAME]],
        extensions: [[$class: 'CloneOption', noTags: false, shallow: false, depth: 0, reference: '']],
        userRemoteConfigs: scm.userRemoteConfigs,
    ])

    stage 'Clean'
    sh 'rm -rf ./ci'
    sh 'mkdir -p ./ci'

    stage 'Compute version name'
    sh 'scripts/ciBuildVersion.sh ${BRANCH_NAME}'

    stage 'Download and cache dependencies'
    sh 'scripts/ciCreateDependencyImage.sh'

    stage 'Run tests'
    catchError(buildResult: 'SUCCESS', stageResult: 'FAILURE') {
        sh 'scripts/ciTest.sh'
    }
    stage 'Publish tests'
    step([$class: 'JUnitResultArchiver', testResults: '**/ci/test-reports/*.xml'])
    
    stage 'Run code coverage measurement'
    catchError(buildResult: 'SUCCESS', stageResult: 'FAILURE') {
        sh 'scripts/ciCodeCoverage.sh'
    }
    stage 'Publish code coverage measurements'
    step([$class: 'JUnitResultArchiver', testResults: '**/ci/code-coverage-reports/coverage.xml'])

    stage 'Build docker image'
    withCredentials(
        [
            usernamePassword(credentialsId: 'DOCKERHUB_CREDENTIAL', usernameVariable: 'DOCKERHUB_USER', passwordVariable: 'DOCKERHUB_TOKEN')
        ]
        ) {
            docker.withRegistry('https://index.docker.io/v1/', 'DOCKERHUB_CREDENTIAL') {
                sh 'scripts/ciBuildDockerImage.sh'
            }
        }
}
